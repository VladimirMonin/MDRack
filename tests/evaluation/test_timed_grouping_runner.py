"""Provider-free A/B/C timed-grouping runner plumbing over the public fixture."""

from __future__ import annotations

import json
from pathlib import Path

from mdrack_media import (
    TOKEN_COUNT_ESTIMATED,
    NormalizationFingerprint,
    ProducerFingerprint,
    TimedTextAtom,
    TokenCounterFingerprint,
    atom_id,
    provisional_abc_policies,
    resource_id,
    run_grouping_variants,
)

ROOT = Path(__file__).resolve().parent / "corpus-v1" / "artifacts"
PRODUCER = ProducerFingerprint.from_payload(
    {"fixture": "public-evaluation-corpus-v1", "projection": "passages-as-atoms"}
)
NORMALIZATION = NormalizationFingerprint.from_payload({"normalization": "identity-v1"})


class EstimatedWordCounter:
    fingerprint = TokenCounterFingerprint.from_payload(
        {"counter": "deterministic-whitespace-estimate-v1"}
    )

    def count(self, text: str) -> int:
        return len(text.split())


def load_atoms(path: Path) -> tuple[TimedTextAtom, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    identifier = resource_id("evaluation-corpus-v1", payload["resource_id"])
    return tuple(
        TimedTextAtom(
            atom_id=atom_id(identifier, PRODUCER.value, ordinal),
            resource_id=identifier,
            start_ms=passage["start_ms"],
            end_ms=passage["end_ms"],
            text=passage["text"],
            ordinal=ordinal,
            producer_fingerprint=PRODUCER,
            normalization_fingerprint=NORMALIZATION,
        )
        for ordinal, passage in enumerate(payload["passages"])
    )


def test_public_audio_video_fixture_runs_all_provisional_grouping_variants() -> None:
    artifact_paths = sorted((*ROOT.glob("audio-*.json"), *ROOT.glob("video-*.json")))
    assert len(artifact_paths) == 20

    fingerprints: dict[str, set[str]] = {"A": set(), "B": set(), "C": set()}
    for path in artifact_paths:
        atoms = load_atoms(path)
        results = run_grouping_variants(
            atoms,
            token_counter=EstimatedWordCounter(),
            token_count_kind=TOKEN_COUNT_ESTIMATED,
            variants=provisional_abc_policies(),
            unsplittable="flag",
        )
        assert [item.variant for item in results] == ["A", "B", "C"]
        for item in results:
            metrics = item.result.metrics
            assert metrics.source_atom_count == len(atoms)
            assert metrics.source_atom_reference_count == len(atoms)
            assert metrics.duplicate_source_atom_count == 0
            assert metrics.output_overlap_count == 0
            assert all(passage.token_count.kind == TOKEN_COUNT_ESTIMATED for passage in item.result.passages)
            fingerprints[item.variant].add(item.result.grouper_fingerprint.value)

    assert all(len(values) == 1 for values in fingerprints.values())
    assert len({next(iter(values)) for values in fingerprints.values()}) == 3
