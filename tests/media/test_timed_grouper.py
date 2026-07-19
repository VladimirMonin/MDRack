from __future__ import annotations

import random
from dataclasses import replace

import pytest

from mdrack_media import (
    TOKEN_COUNT_EXACT,
    GrouperFingerprint,
    NormalizationFingerprint,
    ProducerFingerprint,
    TimedChunkingPolicy,
    TimedGroupingError,
    TimedTextAtom,
    TokenCounterFingerprint,
    atom_id,
    group_timed_atoms,
    provisional_abc_policies,
    resource_id,
    run_grouping_variants,
)

RESOURCE_ID = resource_id("fixture", "timed-grouper")
PRODUCER = ProducerFingerprint.from_payload({"producer": "fixture", "version": 1})
NORMALIZATION = NormalizationFingerprint.from_payload({"normalization": "identity-v1"})


class WordCounter:
    fingerprint = TokenCounterFingerprint.from_payload({"counter": "words-v1"})

    def count(self, text: str) -> int:
        return len(text.split())


COUNTER = WordCounter()


def atom(
    ordinal: int,
    start_ms: int,
    end_ms: int,
    text: str,
    *,
    speaker: str | None = None,
) -> TimedTextAtom:
    return TimedTextAtom(
        atom_id=atom_id(RESOURCE_ID, PRODUCER.value, ordinal),
        resource_id=RESOURCE_ID,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        ordinal=ordinal,
        producer_fingerprint=PRODUCER,
        normalization_fingerprint=NORMALIZATION,
        speaker=speaker,
    )


def policy(**changes: int) -> TimedChunkingPolicy:
    values = {
        "soft_min_tokens": 2,
        "target_tokens": 4,
        "soft_max_tokens": 6,
        "hard_max_tokens": 8,
        "soft_min_duration_ms": 1_000,
        "target_duration_ms": 2_000,
        "soft_max_duration_ms": 3_000,
        "hard_max_duration_ms": 4_000,
        "medium_pause_ms": 300,
        "strong_pause_ms": 700,
        "overlap_atoms": 0,
    }
    values.update(changes)
    return TimedChunkingPolicy(**values)


def grouped(atoms: tuple[TimedTextAtom, ...], **kwargs: object):
    return group_timed_atoms(
        atoms,
        policy=policy(),
        token_counter=COUNTER,
        token_count_kind=TOKEN_COUNT_EXACT,
        resource_identifier=RESOURCE_ID,
        normalization_fingerprint=NORMALIZATION,
        **kwargs,
    )


def test_one_atom_golden_identity_fingerprint_and_serialization() -> None:
    result = grouped((atom(0, 100, 900, "Привет, мир! 🧂"),))

    assert result.grouper_fingerprint == GrouperFingerprint.from_payload(result.fingerprint_payload)
    assert result.grouper_fingerprint.value == (
        "sha256:637d39b230bd99a8cc6e45960a4a71356af1ae67c7766139df87218952537584"
    )
    assert result.passages[0].passage_id == (
        "passage_d864b5ab8fe52ae9db0250b85552fcb9d264eabdb132bae600d69bf242341578"
    )
    assert result.fingerprint_payload["tie_order"] == (
        "score_desc",
        "token_distance_asc",
        "duration_distance_asc",
        "boundary_asc",
    )
    assert result.passages[0].to_dict() == result.to_dict()["passages"][0]
    assert result.passages[0].text == "Привет, мир! 🧂"
    assert result.passages[0].source_atom_ids == (result.atoms[0].atom_id,)


def test_exact_target_and_hard_risk_create_exact_once_ordered_passages() -> None:
    atoms = tuple(
        atom(index, index * 1_000, (index + 1) * 1_000, f"word {index}")
        for index in range(5)
    )

    result = grouped(atoms)

    assert [passage.source_atom_ids for passage in result.passages] == [
        tuple(item.atom_id for item in atoms[:3]),
        tuple(item.atom_id for item in atoms[3:]),
    ]
    assert tuple(source for passage in result.passages for source in passage.source_atom_ids) == tuple(
        item.atom_id for item in atoms
    )
    assert all(
        current.start_ms >= previous.end_ms
        for previous, current in zip(result.passages, result.passages[1:], strict=False)
    )
    assert result.metrics.source_atom_count == len(atoms)
    assert result.metrics.duplicate_source_atom_count == 0


def test_sentence_speaker_pause_and_ties_have_frozen_earliest_tie_order() -> None:
    atoms = (
        atom(0, 0, 500, "one two.", speaker="a"),
        atom(1, 500, 1_000, "three four", speaker="b"),
        atom(2, 1_800, 2_300, "five six.", speaker="b"),
        atom(3, 2_300, 2_800, "seven eight", speaker="a"),
    )

    result = grouped(atoms)

    assert [passage.source_atom_ids for passage in result.passages] == [
        tuple(item.atom_id for item in atoms[:3]),
        (atoms[3].atom_id,),
    ]
    assert result.metrics.boundary_reason_counts == {"end_of_input": 1, "soft_max": 1}


def test_join_preserves_atom_whitespace_and_only_inserts_missing_separator() -> None:
    atoms = (
        atom(0, 0, 500, "  leading"),
        atom(1, 500, 1_000, "trailing  "),
        atom(2, 1_000, 1_500, "\nnext"),
    )

    result = grouped(atoms)

    assert "".join(passage.text for passage in result.passages) == "  leading trailing  \nnext"


def test_overlapping_input_is_preserved_inside_unsplittable_component_with_zero_output_overlap() -> None:
    atoms = (
        atom(0, 0, 1_500, "one"),
        atom(1, 1_000, 2_000, "two"),
        atom(2, 1_900, 2_500, "three"),
        atom(3, 3_000, 3_500, "four"),
        atom(4, 4_000, 4_500, "five"),
    )

    result = grouped(atoms)

    assert result.passages[0].source_atom_ids[:3] == tuple(item.atom_id for item in atoms[:3])
    assert result.passages[0].start_ms == 0
    assert result.passages[0].end_ms == 3_500
    assert result.passages[1].start_ms == 4_000
    assert result.metrics.input_overlap_count == 2
    assert result.metrics.output_overlap_count == 0


def test_unordered_input_fails_strictly_without_hidden_sort_or_repair() -> None:
    atoms = (
        atom(0, 1_000, 1_500, "first"),
        atom(1, 0, 500, "second"),
    )

    with pytest.raises(TimedGroupingError, match="atoms_not_ordered"):
        grouped(atoms)


def test_long_single_atom_rejects_or_is_explicitly_flagged_without_false_limit_claim() -> None:
    long_atom = atom(0, 0, 5_000, "one two three four five six seven eight nine")

    with pytest.raises(TimedGroupingError, match="unsplittable_hard_limit"):
        grouped((long_atom,))

    flagged = grouped((long_atom,), unsplittable="flag")
    passage = flagged.passages[0]
    assert passage.metadata["hard_limit_exceeded"] is True
    assert passage.metadata["unsplittable"] is True
    assert flagged.metrics.unsplittable_passage_count == 1
    assert flagged.metrics.hard_limit_exceeded_count == 1


@pytest.mark.parametrize(
    "limits",
    [
        {"hard_max_tokens": 3, "soft_max_tokens": 3, "target_tokens": 2},
        {
            "hard_max_duration_ms": 1_500,
            "soft_max_duration_ms": 1_500,
            "target_duration_ms": 1_000,
        },
    ],
    ids=["tokens", "duration"],
)
def test_hard_token_and_duration_risk_split_before_exceeding(limits: dict[str, int]) -> None:
    atoms = tuple(
        atom(index, index * 1_000, (index + 1) * 1_000, "one")
        for index in range(4)
    )

    result = group_timed_atoms(
        atoms,
        policy=policy(**limits),
        token_counter=COUNTER,
        token_count_kind=TOKEN_COUNT_EXACT,
    )

    assert len(result.passages) >= 2
    assert result.metrics.hard_limit_exceeded_count == 0
    assert all(
        passage.token_count.count <= policy(**limits).hard_max_tokens
        and passage.end_ms - passage.start_ms <= policy(**limits).hard_max_duration_ms
        for passage in result.passages
    )


def test_empty_input_and_estimated_counts_retain_explicit_contract_identity() -> None:
    empty = grouped(())
    estimated = group_timed_atoms(
        (atom(0, 0, 500, "one"),),
        policy=policy(),
        token_counter=COUNTER,
        token_count_kind="estimated",
    )

    assert empty.passages == ()
    assert empty.metrics.source_atom_count == 0
    assert empty.representation_id.startswith("representation_")
    assert estimated.passages[0].token_count.kind == "estimated"
    assert estimated.passages[0].token_count.counter_fingerprint == COUNTER.fingerprint


def test_overlapping_component_that_exceeds_hard_limits_is_rejected_or_flagged() -> None:
    atoms = (
        atom(0, 0, 1_500, "one two"),
        atom(1, 1_000, 2_500, "three four"),
        atom(2, 2_000, 3_500, "five six"),
    )
    strict_policy = policy(hard_max_tokens=4, soft_max_tokens=4, target_tokens=3)

    with pytest.raises(TimedGroupingError, match="unsplittable_hard_limit"):
        group_timed_atoms(
            atoms,
            policy=strict_policy,
            token_counter=COUNTER,
            token_count_kind=TOKEN_COUNT_EXACT,
        )

    flagged = group_timed_atoms(
        atoms,
        policy=strict_policy,
        token_counter=COUNTER,
        token_count_kind=TOKEN_COUNT_EXACT,
        unsplittable="flag",
    )
    assert flagged.passages[0].source_atom_ids == tuple(item.atom_id for item in atoms)
    assert flagged.passages[0].metadata["unsplittable"] is True


def test_policy_change_churns_fingerprint_representation_and_passage_ids() -> None:
    atoms = (atom(0, 0, 500, "one two"), atom(1, 500, 1_000, "three four"))
    first = grouped(atoms)
    second = group_timed_atoms(
        atoms,
        policy=policy(target_tokens=5),
        token_counter=COUNTER,
        token_count_kind=TOKEN_COUNT_EXACT,
    )

    assert first.grouper_fingerprint != second.grouper_fingerprint
    assert first.representation_id != second.representation_id
    assert [item.passage_id for item in first.passages] != [
        item.passage_id for item in second.passages
    ]


def test_seeded_property_matrix_preserves_coverage_ranges_limits_and_determinism() -> None:
    rng = random.Random(20260719)
    for case in range(200):
        size = rng.randint(0, 30)
        current_start = 0
        atoms: list[TimedTextAtom] = []
        for ordinal in range(size):
            current_start += rng.randint(0, 700)
            duration = rng.randint(1, 900)
            atoms.append(
                atom(
                    ordinal,
                    current_start,
                    current_start + duration,
                    f"слово{case}_{ordinal}" + ("." if rng.random() < 0.3 else ""),
                    speaker=rng.choice((None, "a", "b")),
                )
            )
        values = tuple(atoms)

        first = grouped(values, unsplittable="flag")
        second = grouped(values, unsplittable="flag")

        assert first.to_dict() == second.to_dict()
        assert tuple(source for item in first.passages for source in item.source_atom_ids) == tuple(
            item.atom_id for item in values
        )
        assert len({source for item in first.passages for source in item.source_atom_ids}) == size
        assert all(
            passage.start_ms == min(atom.start_ms for atom in values if atom.atom_id in passage.source_atom_ids)
            and passage.end_ms == max(atom.end_ms for atom in values if atom.atom_id in passage.source_atom_ids)
            for passage in first.passages
        )
        assert first.metrics.output_overlap_count == 0
        assert all(
            passage.token_count.count <= policy().hard_max_tokens
            and passage.end_ms - passage.start_ms <= policy().hard_max_duration_ms
            or passage.metadata["hard_limit_exceeded"] is True
            for passage in first.passages
        )


def test_provisional_abc_runner_is_deterministic_and_reports_truthful_metrics() -> None:
    atoms = tuple(
        atom(index, index * 10_000, (index + 1) * 10_000, f"public atom {index}.")
        for index in range(12)
    )

    policies = provisional_abc_policies()
    first = run_grouping_variants(
        atoms,
        token_counter=COUNTER,
        token_count_kind=TOKEN_COUNT_EXACT,
        variants=policies,
        unsplittable="flag",
    )
    second = run_grouping_variants(
        atoms,
        token_counter=COUNTER,
        token_count_kind=TOKEN_COUNT_EXACT,
        variants=policies,
        unsplittable="flag",
    )

    assert [item.variant for item in first] == ["A", "B", "C"]
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]
    assert all(item.result.metrics.source_atom_count == len(atoms) for item in first)
    assert len({item.result.grouper_fingerprint for item in first}) == 3


def test_atom_identity_or_resource_mismatch_fails_closed() -> None:
    first = atom(0, 0, 500, "one")
    other_resource = resource_id("fixture", "other")
    invalid = replace(
        atom(1, 500, 1_000, "two"),
        resource_id=other_resource,
        atom_id=atom_id(other_resource, PRODUCER.value, 1),
    )

    with pytest.raises(TimedGroupingError, match="mixed_atom_contract"):
        grouped((first, invalid))
